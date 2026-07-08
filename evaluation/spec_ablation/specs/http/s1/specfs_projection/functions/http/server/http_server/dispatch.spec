[PROMPT]
Implement function `dispatch`. Responsibility: 按请求方法分发到对应处理器：GET→handle_get, HEAD→handle_head, POST→handle_post, UNKNOWN→501

[RELY]
- STRUCT `http_request_t`
  role: dispatch 读取或维护的 http_request_t 状态
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

- FUNC `handle_get`
  role: dispatch 调用 handle_get 完成子步骤
```c
static int handle_get(http_server_t* server, http_session_t* session, const http_request_t* req);
```

- FUNC `handle_head`
  role: dispatch 调用 handle_head 完成子步骤
```c
static int handle_head(http_server_t* server, http_session_t* session, const http_request_t* req);
```

- FUNC `handle_post`
  role: dispatch 调用 handle_post 完成子步骤
```c
static int handle_post(http_server_t* server, http_session_t* session, const http_request_t* req);
```

- FUNC `send_error`
  role: dispatch 调用 send_error 完成子步骤
```c
static int send_error(http_server_t* server, http_session_t* session, int code);
```

[GUARANTEE]
```c
static int dispatch(http_server_t* server, http_session_t* session, const http_request_t* req);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server、session、已解析的 req。

**Post-Condition**:
- 成功返回 0，失败返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- server、session、request 均非空；request->method 已由解析器填充
- 请求已被对应处理器处理；未知方法时已调用 send_error(501)
- 不得把 HTTP_UNKNOWN 改写为 400；unsupported method 的唯一响应码是 501

**System Algorithm**:
- switch(req->method)：HTTP_GET→handle_get; HTTP_HEAD→handle_head; HTTP_POST→handle_post; HTTP_UNKNOWN→send_error 501。HTTP_UNKNOWN 是 parser 对合法但不支持 method token 的正常输出，必须在此处映射为 501 Not Implemented。
