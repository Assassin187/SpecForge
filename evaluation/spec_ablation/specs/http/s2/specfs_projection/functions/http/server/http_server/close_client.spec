[PROMPT]
Implement function `close_client`. Responsibility: 关闭客户端连接：委托 http_tcp_server_close_client 关闭 fd 并清理

[RELY]
- FUNC `http_tcp_server_close_client`
  role: close_client 调用 http_tcp_server_close_client 完成子步骤
```c
int http_tcp_server_close_client(http_tcp_server_t* server, int fd);
```

[GUARANTEE]
```c
static void close_client(http_server_t* server, int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 当连接需要关闭时（读错误/对端关闭/响应完成）调用。
- Precondition: fd 为有效客户端 socket；单线程事件循环调用。
- Input: server 和待关闭的 fd。

**Post-Condition**:
- State Change: fd 从 epoll 和客户端链表移除；事件可能产生状态副作用，不承诺幂等。
- Response: 无直接返回值；关闭 fd 后移除并释放对应 session/connection 状态。

**Invariant**:
- None specified.

**System Algorithm**:
- 委托 http_tcp_server_close_client 执行 epoll 删除+socket close+节点移除+on_close 回调。
