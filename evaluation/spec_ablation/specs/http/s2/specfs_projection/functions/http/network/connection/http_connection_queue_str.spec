[PROMPT]
Implement function `http_connection_queue_str`. Responsibility: 将 C 字符串通过 strlen 计算长度后委托 http_connection_queue 追加到输出缓冲

[RELY]
- FUNC `http_connection_queue`
  role: 委托排队原始字节
```c
int http_connection_queue(http_connection_t* conn, const void* data, size_t len);
```

[GUARANTEE]
```c
int http_connection_queue_str(http_connection_t* conn, const char* text);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：conn(http_connection_t*，不可为 NULL，BORROWED)；text(const char*，可为 NULL，BORROWED)。

**Post-Condition**:
- 委托 http_connection_queue 的返回值。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- text==NULL 返回 -1；否则 strlen(text) 后调用 http_connection_queue(conn, text, len)。
