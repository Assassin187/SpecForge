[PROMPT]
Implement function `on_accept_cb`. Responsibility: TCP server 新连接回调：创建会话→加入链表→失败则关闭客户端

[RELY]
- STRUCT `struct http_server`
  role: on_accept_cb 读取或维护的 struct http_server 状态
```c
struct http_server;
```

- FUNC `http_session_create`
  role: on_accept_cb 调用 http_session_create 完成子步骤
```c
http_session_t* http_session_create(int fd);
```

- FUNC `add_session`
  role: on_accept_cb 调用 add_session 完成子步骤
```c
static int add_session(http_server_t* server, http_session_t* session);
```

- FUNC `http_tcp_server_close_client`
  role: on_accept_cb 调用 http_tcp_server_close_client 完成子步骤
```c
int http_tcp_server_close_client(http_tcp_server_t* server, int fd);
```

- FUNC `http_session_destroy`
  role: on_accept_cb 调用 http_session_destroy 完成子步骤
```c
void http_session_destroy(http_session_t* session);
```

[GUARANTEE]
```c
static void on_accept_cb(void* user, int fd, const struct sockaddr_storage* peer, socklen_t peer_len);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: TCP server accept 到新连接时被 http_tcp_server_run 回调。
- Precondition: user 指向有效的 http_server_t 对象；单线程事件循环调用。
- Input: user(强转为 http_server_t*)、fd、peer 地址。

**Post-Condition**:
- State Change: server->sessions 链表新增节点；事件可能产生状态副作用，不承诺幂等。
- Response: 无直接返回值；副作用为会话已加入 server 管理。

**Invariant**:
- None specified.

**System Algorithm**:
- http_session_create(fd) 创建会话→add_session 加入 server->sessions 链表。任一失败则 http_session_destroy 清理并 http_tcp_server_close_client 关闭 fd。
