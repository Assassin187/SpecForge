[PROMPT]
Implement function `http_response_send_html`. Responsibility: 将 printf 风格格式化字符串包装为 text/html 响应并发送

[RELY]
- FUNC `http_response_send`
  role: 发送完整 HTTP 响应
```c
int http_response_send(http_connection_t* conn, int status, const char** extra_headers, const void* body, size_t body_len);
```

[GUARANTEE]
```c
int http_response_send_html(http_connection_t* conn, int status, const char* title, const char* fmt, ...);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 conn、状态码、标题、printf 格式字符串及可变参数。

**Post-Condition**:
- 成功返回 0，格式化溢出或发送失败返回 -1。

**Invariant**:
- HTML 内容缓冲上限 8192 字节
- extra_headers 仅包含单个完整字符串 "Content-Type: text/html"，禁止拆成 "Content-Type" 和 "text/html" 两个数组元素
- title 用于 HTML <title> 元素
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- conn 为已创建的有效连接对象
- fmt 为有效的 printf 格式字符串，变参与格式说明符匹配
- 格式化的 HTML 响应已通过 http_response_send 追加到 conn 的输出缓冲
- 返回 0 表示成功，返回 -1 表示格式化或发送失败

**System Algorithm**:
- vsnprintf 格式化内容到 8192 字节栈缓冲→snprintf 包装为完整 HTML→设置单个完整 header line "Content-Type: text/html" 作为 extra_headers→委托 http_response_send。
