[PROMPT]
Implement function `handle_post`. Responsibility: POST 请求处理器：resolve_path 确定目标→目录返回 405→fopen wb 写入 body→201 Created 响应

[RELY]
- STRUCT `struct http_server`
  role: handle_post 读取或维护的 struct http_server 状态
```c
struct http_server;
```

- STRUCT `http_session_t`
  role: handle_post 读取或维护的 http_session_t 状态
```c
typedef struct http_session {
    int fd;
    http_connection_t* conn;
} http_session_t;
```

- STRUCT `http_request_t`
  role: handle_post 读取或维护的 http_request_t 状态
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

- FUNC `send_error`
  role: handle_post 调用 send_error 完成子步骤
```c
static int send_error(http_server_t* server, http_session_t* session, int code);
```

- FUNC `http_resolve_path`
  role: handle_post 调用 http_resolve_path 完成子步骤
```c
int http_resolve_path(const char* root_dir, const char* uri_path, char* out_abs, size_t out_len);
```

- FUNC `http_response_send`
  role: handle_post 调用 http_response_send 完成子步骤
```c
int http_response_send(http_connection_t* conn, int status, const char** extra_headers, const void* body, size_t body_len);
```

- FUNC `http_tcp_server_update_interest`
  role: handle_post 调用 http_tcp_server_update_interest 完成子步骤
```c
int http_tcp_server_update_interest(http_tcp_server_t* server, int fd, bool want_write);
```

[GUARANTEE]
```c
static int handle_post(http_server_t* server, http_session_t* session, const http_request_t* req);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server、session、req（含 POST body）。

**Post-Condition**:
- 成功返回 0，失败返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- server、session、request 均非空；request->uri 和 request->body 已填充
- 文件创建成功时返回 201；失败时已调用 send_error 返回对应错误码

**System Algorithm**:
- http_resolve_path 解析目标路径→stat 检查→若为目录返回 405→fopen wb 创建文件→fwrite body→fclose→失败则 unlink 并返回 500→snprintf 生成确认 HTML→http_response_send 201→update_interest。
