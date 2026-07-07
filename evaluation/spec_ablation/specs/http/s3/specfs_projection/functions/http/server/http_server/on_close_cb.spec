[PROMPT]
Implement function `on_close_cb`. Responsibility: TCP server 连接关闭回调：从 server 链表移除会话

[RELY]
- STRUCT `struct http_server`
  role: on_close_cb 读取或维护的 struct http_server 状态
```c
struct http_server;
```

- FUNC `remove_session`
  role: on_close_cb 调用 remove_session 完成子步骤
```c
static void remove_session(http_server_t* server, int fd);
```

[GUARANTEE]
```c
static void on_close_cb(void* user, int fd);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: 连接关闭时被 http_tcp_server_close_client 回调。
- Precondition: user 指向有效的 http_server_t；单线程事件循环调用。
- Input: user、fd。

**Post-Condition**:
- State Change: server->sessions 链表减少一个节点；重复触发不得破坏状态一致性。
- Response: 无直接返回值；关闭 fd 后移除并释放对应 session/connection 状态。

**Invariant**:
- None specified.

**System Algorithm**:
- remove_session 从链表删除并销毁会话。
