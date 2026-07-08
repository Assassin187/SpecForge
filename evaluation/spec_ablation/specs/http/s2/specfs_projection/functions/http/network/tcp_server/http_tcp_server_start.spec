[PROMPT]
Implement function `http_tcp_server_start`. Responsibility: 创建监听 socket→epoll_create1→注册 listen_fd 的 EPOLLIN

[RELY]
- STRUCT `struct http_tcp_server`
  role: TCP server 私有状态
```c
struct http_tcp_server;
```

- FUNC `setup_listener`
  role: 创建监听 socket
```c
static int setup_listener(http_tcp_server_t* server);
```

[GUARANTEE]
```c
int http_tcp_server_start(http_tcp_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server 指针。

**Post-Condition**:
- 成功返回 0，失败返回 -1。

**Invariant**:
- 仅在监听 socket 与 epoll 都就绪后进入可运行状态
- 失败时不进入事件循环
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- 调用 setup_listener 创建监听 socket→epoll_create1(0)→epoll_ctl EPOLL_CTL_ADD 注册 listen_fd EPOLLIN。任一步失败清理并返回 -1。
