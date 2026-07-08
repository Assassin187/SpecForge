[PROMPT]
Implement function `smtp_connection_queue_str`. Responsibility: 将 NUL 结尾文本排入输出队列

[RELY]
- STRUCT `smtp_connection_t`
  role: 该函数读取或维护的结构化状态
```c
typedef struct smtp_connection smtp_connection_t;
```

- FUNC `smtp_connection_queue`
  role: 被该函数调用以完成子步骤
```c
int smtp_connection_queue(smtp_connection_t* conn, const void* data, size_t len);
```

- FUNC `strlen`
  role: 被该函数调用以完成子步骤
  declaration: external dependency; canonical declaration unavailable.

[GUARANTEE]
```c
int smtp_connection_queue_str(smtp_connection_t* conn, const char* text);
```

[SPECIFICATION]
**Pre-Condition**:
- 输入参数：conn(smtp_connection_t*，可为 NULL，BORROWED)；text(const char*，可为 NULL，BORROWED)。

**Post-Condition**:
- 返回底层 queue 调用结果；text 为 NULL 时返回 -1。

**Invariant**:
- 仅面向单线程事件循环或单线程调用路径；跨线程访问必须由上层同步。
- 函数可能分配资源、消费缓冲、修改链表、写文件或更新网络状态，不承诺幂等。

**System Algorithm**:
- 将 NUL 结尾文本排入输出队列。
