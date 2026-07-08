[PROMPT]
Implement function `on_event_cb`. Responsibility: TCP event 回调：处理可写 flush、可读 read/pop_line，并分派 DATA/AUTH/command

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

- FUNC `find_session`
  role: 被该函数调用以完成子步骤
```c
static smtp_session_t* find_session(smtp_server_t* server, int fd);
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

- FUNC `smtp_connection_read`
  role: 被该函数调用以完成子步骤
```c
int smtp_connection_read(smtp_connection_t* conn);
```

- FUNC `smtp_connection_pop_line`
  role: 被该函数调用以完成子步骤
```c
char* smtp_connection_pop_line(smtp_connection_t* conn);
```

- FUNC `handle_data_line`
  role: 被该函数调用以完成子步骤
```c
static void handle_data_line(smtp_server_t* server, smtp_session_t* session, const char* line);
```

- FUNC `handle_auth_continuation`
  role: 被该函数调用以完成子步骤
```c
static void handle_auth_continuation(smtp_server_t* server, smtp_session_t* session, const char* line);
```

- FUNC `smtp_command_parse`
  role: 被该函数调用以完成子步骤
```c
int smtp_command_parse(const char* line, smtp_command_t* out);
```

- FUNC `handle_command`
  role: 被该函数调用以完成子步骤
```c
static void handle_command(smtp_server_t* server, smtp_session_t* session, const smtp_command_t* cmd);
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
static void on_event_cb(void* user, int fd, uint32_t events);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 收到客户端 fd 的 SMTP_IO_WRITABLE 和/或 SMTP_IO_READABLE 事件；处理过程中任一 close callback 都可能同步移除并销毁当前 session。
- Precondition: 输入参数：user(void*，可为 NULL，BORROWED)；fd(int，不可为 NULL，BORROWED)；events(uint32_t，不可为 NULL，BORROWED)；遵守单线程事件循环调用约束。
- Input: 输入参数：user(void*，可为 NULL，BORROWED)；fd(int，不可为 NULL，BORROWED)；events(uint32_t，不可为 NULL，BORROWED)。

**Post-Condition**:
- State Change: 可能消费输入缓冲、追加或刷新输出队列、更新写兴趣、推进协议状态或同步关闭连接。close_control/on_close 可在当前调用栈中销毁 session；每个可能失效点之后都必须重新查找，禁止继续使用旧指针。
- Response: 无直接返回值；有效 session 上完成可写 flush 和已缓冲行的协议分发。关闭连接后立即终止本次事件处理，不再访问旧 session->conn，也不执行最终 flush。

**Invariant**:
- None specified.

**System Algorithm**:
- 先通过 find_session(server, fd) 获取 session，找不到时立即返回。SMTP_IO_WRITABLE 置位时调用 flush_control_now；flush 失败则 close_control 并立即 return，成功后才处理其他 event bits。SMTP_IO_READABLE 置位时调用 smtp_connection_read：返回 0 或 -1 表示关闭/致命错误，必须 close_control 并立即 return；返回 -2 仅表示当前无更多字节，仍必须处理输入缓冲中已经存在的完整行。循环 pop_line 时，超长行排队 500 后不得销毁 session，DATA mode 优先调用 handle_data_line，AUTH continuation 调用 handle_auth_continuation，否则解析命令并调用 handle_command。每次 handler 返回且 line 已释放后，都必须重新执行 session = find_session(server, fd)；若返回 NULL 则立即 return，只有重新确认有效后才能继续 pop_line 或最终 flush。handle_command、超长行策略以及任何可能调用 close_control 的路径都必须视为 session-invalidating call。
