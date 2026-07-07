[PROMPT]
Implement function `http_tcp_server_update_interest`. Responsibility: 更新 fd 的 epoll 监听事件（基础 EPOLLIN|EPOLLRDHUP，按需追加 EPOLLOUT）

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

- FUNC `find_client`
  role: 按 fd 定位客户端节点
```c
static http_client_node_t* find_client(http_tcp_server_t* server, int fd);
```

[GUARANTEE]
```c
int http_tcp_server_update_interest(http_tcp_server_t* server, int fd, bool want_write);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server、fd 和 want_write 标志。

**Post-Condition**:
- 成功返回 0，失败返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- 调用方必须遵守 SIGNATURE.PARAMS 中的 NULLABLE 与 OWNERSHIP 约束。

**System Algorithm**:
- find_client 查找节点→若 want_write 状态未变直接返回 0→epoll_ctl EPOLL_CTL_MOD 更新事件掩码→更新 node->want_write。
