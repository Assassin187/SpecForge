[PROMPT]
Implement function `accept_loop`. Responsibility: 循环 accept 所有待处理连接：每轮先初始化 peer_len，成功加入 epoll 后才触发 on_accept

[RELY]
- STRUCT `smtp_tcp_server_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_tcp_server smtp_tcp_server_t;
```

- FUNC `accept`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

- FUNC `set_nonblocking`
  role: 被该函数调用以完成子步骤
```c
static int set_nonblocking(int fd);
```

- FUNC `add_client`
  role: 被该函数调用以完成子步骤
```c
static int add_client(smtp_tcp_server_t* server, int fd);
```

[GUARANTEE]
```c
static void accept_loop(smtp_tcp_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(smtp_tcp_server_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 无返回值；每个成功加入 epoll/client 链表的待处理连接恰好触发一次 on_accept，accept 或 add_client 失败的连接不触发 callback，失败不改变 listener/event loop 的运行状态。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。
- 维护 server->clients：只新增已成功设置非阻塞并加入 epoll 的客户端
- 每次 accept 前 peer_len 都等于 sizeof(peer)，不能复用上一次 accept 修改后的长度
- on_accept 只能观察仍然打开且已由 tcp server 管理的 client fd

**System Algorithm**:
- server 为 NULL 时立即返回。每轮 accept 都重新声明 struct sockaddr_storage peer 并在调用前设置 socklen_t peer_len = sizeof(peer)，不得在 accept 返回后才初始化 peer_len。accept 因 EINTR 失败时继续重试，因 EAGAIN/EWOULDBLOCK 失败时正常结束当前 accept loop，其他错误也只结束当前 accept loop而不得停止 listener/event loop。仅当 accept 返回有效 client fd 且 add_client 成功后才调用 on_accept；add_client 失败时关闭该 client fd，且不得触发 on_accept。
