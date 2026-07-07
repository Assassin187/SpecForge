[PROMPT]
Implement function `smtp_tcp_server_run`. Responsibility: 进入 epoll_wait 主事件循环并把内核事件转换为 SMTP_IO_* 回调

[RELY]
- STRUCT `smtp_tcp_server_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_tcp_server smtp_tcp_server_t;
```

- FUNC `epoll_wait`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

- FUNC `accept_loop`
  role: 被该函数调用以完成子步骤
```c
static void accept_loop(smtp_tcp_server_t* server);
```

- FUNC `find_client`
  role: 被该函数调用以完成子步骤
```c
static smtp_client_node_t* find_client(smtp_tcp_server_t* server, int fd);
```

- FUNC `smtp_tcp_server_close_client`
  role: 被该函数调用以完成子步骤
```c
int smtp_tcp_server_close_client(smtp_tcp_server_t* server, int fd);
```

[GUARANTEE]
```c
int smtp_tcp_server_run(smtp_tcp_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 检查 listen_fd/epoll_fd 有效后设置 running=true；循环 epoll_wait；listen_fd 事件调用 accept_loop，客户端错误/挂断关闭连接，可读/可写事件映射为 SMTP_IO_READABLE/SMTP_IO_WRITABLE 后调用 on_event。
- Precondition: 输入参数：server(smtp_tcp_server_t*，可为 NULL，BORROWED)；遵守单线程事件循环调用约束。
- Input: 输入参数：server(smtp_tcp_server_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- State Change: 可能更新 server->running(进入循环时置 true)；事件可能产生状态副作用，不承诺幂等。
- Response: 成功运行到 stop 后返回 0；server、listen_fd 或 epoll_fd 无效时返回 -1。

**Invariant**:
- None specified.

**System Algorithm**:
- 检查 listen_fd/epoll_fd 有效后设置 running=true；循环 epoll_wait；listen_fd 事件调用 accept_loop，客户端错误/挂断关闭连接，可读/可写事件映射为 SMTP_IO_READABLE/SMTP_IO_WRITABLE 后调用 on_event。
