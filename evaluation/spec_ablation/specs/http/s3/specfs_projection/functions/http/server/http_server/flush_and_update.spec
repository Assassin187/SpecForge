[PROMPT]
Implement function `flush_and_update`. Responsibility: 刷写连接输出缓冲并根据剩余数据更新 epoll 写监听

[RELY]
- STRUCT `http_session_t`
  role: flush_and_update 读取或维护的 http_session_t 状态
```c
typedef struct http_session {
    int fd;
    http_connection_t* conn;
} http_session_t;
```

- FUNC `http_connection_flush`
  role: flush_and_update 调用 http_connection_flush 完成子步骤
```c
int http_connection_flush(http_connection_t* conn);
```

- FUNC `http_connection_has_pending`
  role: flush_and_update 调用 http_connection_has_pending 完成子步骤
```c
bool http_connection_has_pending(const http_connection_t* conn);
```

- FUNC `http_tcp_server_update_interest`
  role: flush_and_update 调用 http_tcp_server_update_interest 完成子步骤
```c
int http_tcp_server_update_interest(http_tcp_server_t* server, int fd, bool want_write);
```

[GUARANTEE]
```c
static int flush_and_update(http_server_t* server, http_session_t* session);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server 和 session。

**Post-Condition**:
- 返回值表达http_connection_flush 刷写→http_connection_has_pending 检查剩余→http_tcp_server_update_interest 按需更新的结果；成功、失败和特殊分支按当前 C 实现的返回码区分。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- server 和 session 均非空；server->tcp 已初始化
- 输出缓冲数据已尽可能发送；epoll 监听事件已根据残留数据更新

**System Algorithm**:
- http_connection_flush 刷写→http_connection_has_pending 检查剩余→http_tcp_server_update_interest 按需更新。
