[PROMPT]
Implement function `smtp_tcp_server_create`. Responsibility: 创建 TCP server 运行时对象并记录端口、回调和 user 指针

[RELY]
- STRUCT `smtp_tcp_server_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_tcp_server smtp_tcp_server_t;
```

- STRUCT `smtp_tcp_callbacks_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_tcp_callbacks {
    void (* on_accept)(void* user, int fd, const struct sockaddr_storage* peer, socklen_t peer_len);
    void (* on_event)(void* user, int fd, uint32_t events);
    void (* on_close)(void* user, int fd);
} smtp_tcp_callbacks_t;
```

[GUARANTEE]
```c
smtp_tcp_server_t* smtp_tcp_server_create(uint16_t port, smtp_tcp_callbacks_t callbacks, void* user);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：port(uint16_t，不可为 NULL，BORROWED)；callbacks(smtp_tcp_callbacks_t，不可为 NULL，BORROWED)；user(void*，可为 NULL，BORROWED)。

**Post-Condition**:
- 成功返回新分配并初始化的对象指针；分配或依赖初始化失败返回 NULL，并清理已分配资源。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 维护 server->listen_fd：初始化为 -1
- 维护 server->epoll_fd：初始化为 -1

**System Algorithm**:
- 创建 TCP server 运行时对象并记录端口、回调和 user 指针。
