[PROMPT]
Implement function `handle_head`. Responsibility: HEAD 请求处理器：委托 serve_file(head_only=true)，仅发送头部不含正文，但保留与 GET 同资源一致的 Content-Length

[RELY]
- FUNC `serve_file`
  role: handle_head 调用 serve_file 完成子步骤
```c
static int serve_file(http_server_t* server, http_session_t* session, const http_request_t* req, bool head_only);
```

[GUARANTEE]
```c
static int handle_head(http_server_t* server, http_session_t* session, const http_request_t* req);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server、session、已解析的 req。

**Post-Condition**:
- 透传 serve_file 返回值。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 重复调用不得破坏内部一致性；无匹配对象、空输入或已完成状态按当前实现安全处理。
- server、session、request 均非空；request->uri 已填充
- 包含 Content-Length 等头部的响应已排队，无 body
- Content-Length 必须等于同一资源 GET 响应的 body 长度，不能因为不发送 body 而写为 0

**System Algorithm**:
- 委托 serve_file(server, session, req, true)。head_only=true 只禁止发送 body 字节，不表示 Content-Length 为 0；serve_file 必须读取或 stat 资源长度，并以该长度作为 advertised Content-Length。
