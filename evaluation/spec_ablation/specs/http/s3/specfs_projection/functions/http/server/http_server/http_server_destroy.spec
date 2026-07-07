[PROMPT]
Implement function `http_server_destroy`. Responsibility: 遍历销毁所有活跃会话→停止并销毁 TCP server→释放 server；空指针安全

[RELY]
- STRUCT `struct http_server`
  role: http_server_destroy 读取或维护的 struct http_server 状态
```c
struct http_server;
```

- STRUCT `http_session_node_t`
  role: http_server_destroy 读取或维护的 http_session_node_t 状态
```c
typedef struct http_session_node {
    http_session_t* session;
    http_session_node_t* next;
} http_session_node_t;
```

- FUNC `http_session_destroy`
  role: http_server_destroy 调用 http_session_destroy 完成子步骤
```c
void http_session_destroy(http_session_t* session);
```

- FUNC `http_tcp_server_destroy`
  role: http_server_destroy 调用 http_tcp_server_destroy 完成子步骤
```c
void http_tcp_server_destroy(http_tcp_server_t* server);
```

[GUARANTEE]
```c
void http_server_destroy(http_server_t* server);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：server(http_server_t*，可为 NULL，BORROWED)。

**Post-Condition**:
- 无返回值；释放本对象持有的内存、连接、队列或子对象，NULL 输入安全返回。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- 无强制前置条件；server 可为 NULL
- server 为 NULL 时立即返回；否则所有会话节点、TCP server 及 server 自身均已释放

**System Algorithm**:
- 空安全检查→遍历 sessions 链表逐一 http_session_destroy+free 节点→http_tcp_server_destroy→free(server)。
