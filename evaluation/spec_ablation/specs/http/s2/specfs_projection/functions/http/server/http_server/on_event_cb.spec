[PROMPT]
Implement function `on_event_cb`. Responsibility: TCP server 数据/写就绪事件回调：按事件类型分发到读/写/关闭处理

[RELY]
- STRUCT `struct http_server`
  role: on_event_cb 读取或维护的 struct http_server 状态
```c
struct http_server;
```

- STRUCT `http_session_t`
  role: on_event_cb 读取或维护的 http_session_t 状态
```c
typedef struct http_session {
    int fd;
    http_connection_t* conn;
} http_session_t;
```

- STRUCT `http_request_t`
  role: on_event_cb 读取或维护的 http_request_t 状态
```c
typedef struct http_request {
    http_method_t method;
    char uri[HTTP_MAX_URI];
    http_header_t headers[HTTP_MAX_HEADERS];
    size_t header_count;
    char* body;
    size_t body_len;
} http_request_t;
```

- FUNC `find_session`
  role: on_event_cb 调用 find_session 完成子步骤
```c
static http_session_t* find_session(http_server_t* server, int fd);
```

- FUNC `http_connection_read`
  role: on_event_cb 调用 http_connection_read 完成子步骤
```c
int http_connection_read(http_connection_t* conn);
```

- FUNC `close_client`
  role: on_event_cb 调用 close_client 完成子步骤
```c
static void close_client(http_server_t* server, int fd);
```

- FUNC `http_request_init`
  role: on_event_cb 调用 http_request_init 完成子步骤
```c
void http_request_init(http_request_t* req);
```

- FUNC `http_request_parse`
  role: on_event_cb 调用 http_request_parse 完成子步骤
```c
int http_request_parse(http_request_t* req, http_connection_t* conn);
```

- FUNC `http_request_free`
  role: on_event_cb 调用 http_request_free 完成子步骤
```c
void http_request_free(http_request_t* req);
```

- FUNC `dispatch`
  role: on_event_cb 调用 dispatch 完成子步骤
```c
static int dispatch(http_server_t* server, http_session_t* session, const http_request_t* req);
```

- FUNC `send_error`
  role: on_event_cb 调用 send_error 完成子步骤
```c
static int send_error(http_server_t* server, http_session_t* session, int code);
```

- FUNC `flush_and_update`
  role: on_event_cb 调用 flush_and_update 完成子步骤
```c
static int flush_and_update(http_server_t* server, http_session_t* session);
```

- FUNC `http_connection_has_pending`
  role: on_event_cb 调用 http_connection_has_pending 完成子步骤
```c
bool http_connection_has_pending(const http_connection_t* conn);
```

- FUNC `http_connection_flush`
  role: on_event_cb 调用 http_connection_flush 完成子步骤
```c
int http_connection_flush(http_connection_t* conn);
```

- FUNC `http_tcp_server_update_interest`
  role: on_event_cb 调用 http_tcp_server_update_interest 完成子步骤
```c
int http_tcp_server_update_interest(http_tcp_server_t* server, int fd, bool want_write);
```

[GUARANTEE]
```c
static void on_event_cb(void* user, int fd, uint32_t events);
```

[SPECIFICATION]
**Pre-Condition**:
- Trigger: epoll 报告客户端 fd 有事件时被 http_tcp_server_run 回调。
- Precondition: user 指向有效的 http_server_t，fd 对应活跃会话；单线程事件循环调用。
- Input: user、fd、epoll 事件标志位。

**Post-Condition**:
- State Change: 可能创建/消费请求、发送响应、关闭连接；事件可能产生状态副作用，不承诺幂等。
- Response: 无直接返回值；根据读写事件刷新输出、读取输入、解析协议消息、分发业务处理或关闭连接。

**Invariant**:
- None specified.

**System Algorithm**:
- HTTP_IO_WRITABLE：flush_and_update→若无剩余待发数据则 close_client。HTTP_IO_READABLE：http_connection_read→读取 0 或错误则 close_client→否则 http_request_parse→0 完成则 dispatch→flush_and_update→若无剩余待发数据则 close_client；parse 返回 1 表示需更多数据且不发送响应；parse 返回 -1 表示 malformed request，必须 send_error(400)→flush_and_update→close_client。任何成功或错误响应都必须先 flush 已排队字节，再关闭连接。
