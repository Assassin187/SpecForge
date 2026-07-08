[PROMPT]
Implement function `http_response_send`. Responsibility: 构建完整 HTTP/1.1 响应并排队到连接输出缓冲：状态行→Date→Server→Content-Length→Connection:close→可选 extra_headers→空白行→可选正文

[RELY]
- FUNC `queuefv`
  role: 格式化并排队字符串到连接输出缓冲
```c
static int queuefv(http_connection_t* conn, const char* fmt, ...);
```

- FUNC `http_status_text`
  role: 将状态码映射为状态文本
```c
const char* http_status_text(int code);
```

- FUNC `http_connection_queue_str`
  role: 排队字符串字面量到连接输出缓冲
```c
int http_connection_queue_str(http_connection_t* conn, const char* text);
```

- FUNC `http_connection_queue`
  role: 排队字节数据到连接输出缓冲
```c
int http_connection_queue(http_connection_t* conn, const void* data, size_t len);
```

[GUARANTEE]
```c
int http_response_send(http_connection_t* conn, int status, const char** extra_headers, const void* body, size_t body_len);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入 conn、状态码、额外头部数组、正文字节和长度。

**Post-Condition**:
- 成功返回 0，失败返回 -1。

**Invariant**:
- extra_headers 为 NULL 终止的字符串数组，每项为完整 Name: value header line 且必须包含冒号
- extra_headers 禁止解释为 name/value 交替数组，不能生成 {"Content-Type", "text/plain", NULL} 调用形态
- body_len 始终是 advertised Content-Length；即使 body 为 NULL，也必须按 body_len 输出 Content-Length
- body 为 NULL 时跳过正文发送；允许 body=NULL 且 body_len>0，用于 HEAD 响应只发送头部但保留实体长度
- Date 头部使用 GMT 时间格式
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。
- conn 为已创建的有效连接对象
- extra_headers 为 NULL 或以 NULL 终止的 "Name: value" 字符串数组
- body 非 NULL 时 body_len 必须为正文实际长度；HEAD 等无 body 响应可以 body 为 NULL 且 body_len 为原始资源长度
- 完整 HTTP/1.1 响应字节序列已追加到 conn 的输出缓冲
- 返回 0 表示成功，返回 -1 表示队列操作失败

**System Algorithm**:
- 依次排队：queuefv 生成状态行→time/gmtime_r/strftime 生成 Date 头部→Server: httpd/1.0→始终用 body_len 输出 Content-Length→Connection: close→遍历 extra_headers 数组并把每一项作为完整 header line 原样追加再追加 \r\n→空白行 \r\n→body（仅当 body 非 NULL 且 body_len > 0 时写出 body 字节）。任一步失败返回 -1。
