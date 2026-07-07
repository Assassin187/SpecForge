[PROMPT]
Implement function `smtp_tcp_server_start`. Responsibility: 初始化监听 socket 和 epoll，并把 listen_fd 注册为 EPOLLIN

[RELY]
- STRUCT `smtp_tcp_server_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_tcp_server smtp_tcp_server_t;
```

- FUNC `setup_listener`
  role: 被该函数调用以完成子步骤
```c
static int setup_listener(smtp_tcp_server_t* server);
```

- FUNC `epoll_create1`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

- FUNC `epoll_ctl`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

[GUARANTEE]
```c
int smtp_tcp_server_start(smtp_tcp_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_tcp_server_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回底层启动结果 0/true；参数无效或底层启动失败返回 -1/false。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 维护 server->listen_fd：监听 fd 必须有效
- 维护 server->epoll_fd：写入 epoll fd

**System Algorithm**:
- 初始化监听 socket 和 epoll，并把 listen_fd 注册为 EPOLLIN。
