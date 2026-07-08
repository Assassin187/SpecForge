[PROMPT]
Implement function `handle_get`. Responsibility: GET 请求处理器：委托 serve_file(head_only=false)

[RELY]
- FUNC `serve_file`
  role: handle_get 调用 serve_file 完成子步骤
```c
static int serve_file(http_server_t* server, http_session_t* session, const http_request_t* req, bool head_only);
```

[GUARANTEE]
```c
static int handle_get(http_server_t* server, http_session_t* session, const http_request_t* req);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server、session、已解析的 req。

**Post-Condition**:
- 透传 serve_file 返回值。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- server、session、request 均非空；request->uri 已填充
- 响应已交由 serve_file/send_error 排入输出队列

**System Algorithm**:
- 委托 serve_file(server, session, req, false)。
