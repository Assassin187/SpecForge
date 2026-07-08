[PROMPT]
Implement function `add_client`. Responsibility: 为已 accept 的 fd 分配客户端节点并注册到 epoll（EPOLLIN|EPOLLRDHUP）

[RELY]
- STRUCT `struct http_tcp_server`
  role: TCP server 私有状态
```c
struct http_tcp_server;
```

- STRUCT `http_client_node_t`
  role: 客户端链表节点
```c
typedef struct http_client_node http_client_node_t;
```

[GUARANTEE]
```c
static int add_client(http_tcp_server_t* server, int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server 和已 accept 的 fd。

**Post-Condition**:
- 成功返回 0，失败返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- calloc 分配节点；epoll_ctl EPOLL_CTL_ADD 注册 EPOLLIN|EPOLLRDHUP；失败时 free 节点返回 -1；成功则设置 fd 并头插法加入 server->clients 链表。
