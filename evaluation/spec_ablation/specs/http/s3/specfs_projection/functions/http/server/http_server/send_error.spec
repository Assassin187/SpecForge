[PROMPT]
Implement function `send_error`. Responsibility: 排队 HTML 错误响应并注册写监听；调用方若要关闭连接，必须先 flush 已排队响应

[RELY]
- FUNC `http_status_text`
  role: send_error 调用 http_status_text 完成子步骤
```c
const char* http_status_text(int code);
```

- FUNC `http_response_send`
  role: send_error 调用 http_response_send 完成子步骤
```c
int http_response_send(http_connection_t* conn, int status, const char** extra_headers, const void* body, size_t body_len);
```

- FUNC `http_tcp_server_update_interest`
  role: send_error 调用 http_tcp_server_update_interest 完成子步骤
```c
int http_tcp_server_update_interest(http_tcp_server_t* server, int fd, bool want_write);
```

[GUARANTEE]
```c
static int send_error(http_server_t* server, http_session_t* session, int code);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 server、session、HTTP 状态码。

**Post-Condition**:
- 返回值表达snprintf 生成 HTML 错误页面→http_response_send 发送→http_tcp_server_update_interest 注册写事件的结果；成功、失败和特殊分支按当前 C 实现的返回码区分。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- session 非空；status_code 为标准 HTTP 错误码（400/403/404/405/413/500/501）
- HTTP 错误响应已排入连接的输出缓冲队列
- 错误响应必须包含 status line、Content-Length 与 Connection: close，并最终可被客户端完整读取
- send_error 成功不代表字节已经写入 socket；关闭连接前必须先 flush 已排队响应

**System Algorithm**:
- snprintf 生成 HTML 错误页面→http_response_send 将完整错误响应排入连接输出缓冲→http_tcp_server_update_interest 注册写事件。本函数不直接关闭连接；调用方在关闭连接前必须先调用 flush_and_update 或等 WRITABLE 分支 flush 完成。
